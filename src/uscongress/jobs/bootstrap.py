"""Fetch the generated repositories onto a machine that does not have them.

A fresh clone of this pipeline arrives with an empty ``data/``, against roughly
93 GB on a machine that has been building: 82 GB of cached upstream XML and 10 GB
of built repositories. The obvious reading is that the corpus has to be rebuilt
from govinfo, and that reading is expensive and wrong. The repositories already
exist on GitHub. Cloning them costs minutes; rebuilding them costs days --
the Congressional Record alone is a 1.37 million request crawl.

So this exists to make the cheap path the discoverable one.

The cache under ``data/raw/`` is deliberately *not* restored. It is an
optimisation, not state: every job refetches what it needs and caches it again.
Only the repositories are worth moving, because they hold work that cannot be
recreated cheaply -- the arrangement of the history itself.

Fetching is blobless. ``--filter=blob:none`` brings commits and trees and leaves
file contents to be fetched on demand, which for the 119th Congress is 7.4 MiB
against a full clone. The refs are the real cost at this scale: that same fetch
writes 18,046 loose ref files, which is where its 37 seconds went, so
``git pack-refs`` runs afterwards and collapses them into one file.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..gitbuild import GitRepo
from ..registry import OWNER, PIPELINE_REPO, PREFIX
from . import publish


def remote_repositories() -> list[str]:
    """List the project's repositories as GitHub knows them.

    Deliberately not :func:`describe.repositories`, which expands shard families
    by globbing ``data/repos``. That is right everywhere else and exactly wrong
    here: a fresh clone has an empty ``data/``, so it would report the three
    unsharded repositories, quietly omit all 29 shards, and leave the machine
    looking bootstrapped. The authority for what exists has to be the place the
    repositories actually are.

    Returns:
        Repository names under the owner, prefixed ``us-congress-``, sorted with
        shard numbers in numeric order so the 109th does not follow the 110th.
    """
    names: list[str] = []
    page = 1
    # A backstop, not a limit: 32 repositories is one page, and the loop already
    # stops on an empty one. It is here because the alternative to a wrong
    # termination condition is an unbounded loop against someone else's API, and
    # a test of this function did exactly that -- `"page=1" in "...per_page=100"`
    # is true, so every page looked like the first.
    while page <= 20:
        result = subprocess.run(
            ["gh", "api", f"users/{OWNER}/repos?per_page=100&page={page}",
             "--jq", ".[].name"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            break
        batch = [n.strip() for n in result.stdout.splitlines() if n.strip()]
        if not batch:
            break
        names += batch
        page += 1

    def key(name: str) -> tuple[str, int]:
        tail = name.rsplit("-", 1)[-1]
        return (name.rsplit("-", 1)[0], int(tail)) if tail.isdigit() else (name, 0)

    return sorted((n for n in names if n.startswith(PREFIX)), key=key)


@dataclass
class Fetched:
    """What bootstrapping one repository achieved.

    Attributes:
        name: Repository name.
        refs: Branches present locally afterwards.
        created: Whether the local repository did not exist before.
        skipped: Why it was skipped, if it was.
    """

    name: str
    refs: int = 0
    created: bool = False
    skipped: str = ""


def fetch_one(name: str, repos_dir: Path | None = None) -> Fetched:
    """Clone or update one generated repository.

    Idempotent, like every other job here: an existing local repository is
    fetched rather than recloned, and one already matching the remote does no
    work at all.

    Args:
        name: Repository name without the owner.
        repos_dir: Where repositories live; defaults to ``data/repos``.

    Returns:
        What happened.
    """
    root = repos_dir or config.REPOS_DIR
    path = root / name
    url = publish.repo_url(name)

    if not publish.remote_exists(url):
        # A repository named in the registry but not yet created is the ordinary
        # state of a planned phase, not an error.
        return Fetched(name, skipped="not created on GitHub yet")

    created = not (path / ".git").is_dir()
    repo = publish.prepare_all(path, url)
    # Loose refs dominate at this scale -- 18,046 of them for the 119th -- so
    # they are packed once here rather than left for git to trip over later.
    subprocess.run(
        ["git", "-C", str(path), "pack-refs", "--all"],
        capture_output=True,
        check=False,
    )
    return Fetched(name, refs=len(repo.ref_map()), created=created)


def run(repos_dir: Path | None = None, only: list[str] | None = None) -> list[Fetched]:
    """Bootstrap every generated repository the registry knows about.

    Args:
        repos_dir: Where repositories live; defaults to ``data/repos``.
        only: Restrict to these repository names. None does all of them.

    Returns:
        One record per repository, in registry order.
    """
    root = repos_dir or config.REPOS_DIR
    root.mkdir(parents=True, exist_ok=True)

    wanted = [n for n in remote_repositories() if n != PIPELINE_REPO]
    if only:
        wanted = [n for n in wanted if n in set(only)]

    results = []
    for name in wanted:
        result = fetch_one(name, root)
        results.append(result)
        if result.skipped:
            print(f"  {name}: {result.skipped}", flush=True)
        else:
            verb = "cloned" if result.created else "updated"
            print(f"  {name}: {verb}, {result.refs:,} refs", flush=True)
    return results


def report(results: list[Fetched]) -> int:
    """Summarise a bootstrap run.

    Args:
        results: What :func:`run` returned.

    Returns:
        How many repositories were skipped because they do not exist yet.
    """
    fetched = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]
    refs = sum(r.refs for r in fetched)
    print(
        f"\n{len(fetched)} repositories, {refs:,} refs. "
        f"The cache under data/raw/ is not restored and does not need to be: "
        f"every job refetches what it needs.",
        flush=True,
    )
    if skipped:
        print(f"{len(skipped)} not created on GitHub yet: "
              + ", ".join(r.name for r in skipped), flush=True)
    return len(skipped)


def bootstrap_needed(repos_dir: Path | None = None) -> list[str]:
    """Return the registry repositories that are missing locally.

    Lets a caller tell "nothing to do" from "this machine has never been
    bootstrapped", which is the question a fresh clone actually has.

    Args:
        repos_dir: Where repositories live; defaults to ``data/repos``.

    Returns:
        Names with no local repository.
    """
    root = repos_dir or config.REPOS_DIR
    return [
        name
        for name in remote_repositories()
        if name != PIPELINE_REPO and not (root / name / ".git").is_dir()
    ]
