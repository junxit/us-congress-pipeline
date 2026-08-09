"""Publish a repository that was rewritten locally, pushing only what moved.

``seed-bills --rebuild`` has always ended by telling the operator it "needs a
force push afterwards", and nothing in the project provided one. The daily loop
pushes only the measures govinfo reported as changed, so a corpus-wide
correction had no path to GitHub except a hand-written loop -- which is exactly
where the batch size that GitHub actually accepts gets forgotten.

The set to push is computed rather than assumed. A rebuild rewrites every branch
from its root, and a branch whose content did not change re-renders to identical
bytes and keeps its SHA, so comparing the local refs with the remote's names the
branches that genuinely differ. For phase 8 that is the difference between
pushing 7,510 branches and pushing 160,190: only 4.4% of measures carry a
recorded vote, and the rest must not be touched at all.

Nothing here decides *whether* to publish. It reports what would move, and only
pushes when told to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..gitbuild import GitRepo
from . import publish


@dataclass
class Divergence:
    """How a local repository differs from what is published.

    Attributes:
        name: Repository name.
        moved: Branches whose local SHA differs from the remote's.
        added: Branches that exist locally and not on the remote.
        remote_only: Branches published but no longer built locally. These are
            reported and never deleted; a branch that vanished from a local
            build is far more likely to be a broken build than a measure that
            ceased to exist.
        unchanged: How many branches match exactly.
        error: Why the comparison could not be made, if it could not.
    """

    name: str
    moved: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    remote_only: list[str] = field(default_factory=list)
    unchanged: int = 0
    error: str = ""

    @property
    def to_push(self) -> list[str]:
        """Branches that need publishing, moved and new together."""
        return sorted(self.moved + self.added)


def compare(path: Path, name: str, token: str = "") -> Divergence:
    """Compare a local repository against its remote.

    Args:
        path: Local repository.
        name: Repository name on GitHub.
        token: Credential, when the remote needs one to be read.

    Returns:
        What differs.
    """
    if not (path / ".git").is_dir():
        return Divergence(name=name, error="not built locally")
    url = publish.repo_url(name, token)
    if not publish.remote_exists(publish.repo_url(name)):
        return Divergence(name=name, error="no such repository on GitHub")

    local = GitRepo(path).ref_map()
    published = publish.remote_refs(url)

    divergence = Divergence(name=name)
    for branch, sha in sorted(local.items()):
        if branch not in published:
            divergence.added.append(branch)
        elif published[branch] != sha:
            divergence.moved.append(branch)
        else:
            divergence.unchanged += 1
    divergence.remote_only = sorted(set(published) - set(local))
    return divergence


def run(
    names: list[str],
    token: str = "",
    dry_run: bool = False,
    repos_dir: Path | None = None,
) -> int:
    """Report -- and optionally publish -- what a local rewrite changed.

    Args:
        names: Repository names to consider.
        token: Credential for pushing. Required unless ``dry_run``.
        dry_run: Compare and report, pushing nothing.
        repos_dir: Where the repositories live.

    Returns:
        Process exit status: non-zero if anything failed or did not land.
    """
    root = repos_dir or config.REPOS_DIR
    failures = 0
    total_pushed = 0

    for name in names:
        divergence = compare(root / name, name, token)
        if divergence.error:
            print(f"{name}: {divergence.error}", flush=True)
            failures += 1
            continue

        pending = divergence.to_push
        detail = (
            f"{len(divergence.moved):,} moved, {len(divergence.added):,} new, "
            f"{divergence.unchanged:,} unchanged"
        )
        if divergence.remote_only:
            # Never deleted. A branch that disappeared from a local build is
            # far more likely to be a build that failed part way than a measure
            # that stopped existing, and a push cannot tell the difference.
            detail += (
                f", {len(divergence.remote_only):,} published but not built here"
            )
        print(f"{name}: {detail}", flush=True)

        if not pending:
            continue
        if dry_run:
            print(f"  would push {len(pending):,} refs", flush=True)
            continue
        if not token:
            print(f"  cannot push {len(pending):,} refs: GITHUB_TOKEN is empty", flush=True)
            failures += 1
            continue

        report = publish.push(root / name, publish.repo_url(name, token), pending)
        total_pushed += len(report.pushed)
        print(
            f"  {len(report.pushed):,} refs published in {report.attempts} attempt(s)",
            flush=True,
        )
        if report.missing:
            # Read back from the remote, not taken from git's exit status.
            print(
                f"  WARNING: {len(report.missing):,} refs did not land"
                + (f" — {report.errors[-1]}" if report.errors else "")
                + "; first: "
                + ", ".join(report.missing[:10]),
                flush=True,
            )
            failures += 1

    if not dry_run:
        print(f"\n{total_pushed:,} refs published across {len(names)} repositories", flush=True)
    return 1 if failures else 0
