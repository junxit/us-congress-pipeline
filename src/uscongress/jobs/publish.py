"""Move refs between here and GitHub, with the failures GitHub actually has.

Everything in this module exists because of something measured against the live
service rather than read in documentation. Pushing a generated repository is not
``git push``; at this ref count it has three distinct failure modes, and two of
them look like success.

* **A single push of ~10,000 refs is rejected outright**, with ``Internal Server
  Error``, atomically, and only *after* transferring everything. Zero refs land.
  Batching at :data:`BATCH` keeps each request inside whatever the real limit
  is; 10,625 refs took 320 seconds that way.
* **Individual refs fail transiently** with the same error, about one in 2,000
  during the 119th. git reports the push as successful. So push output is not
  evidence: what landed is read back with ``ls-remote`` and compared against
  local, and the difference is pushed again.
* **The default branch becomes the first branch pushed**, which alphabetically
  is ``hconres-1`` for a bills repository -- landing every visitor on a random
  bill instead of the README. It has to be set explicitly afterwards.

Fetching has its own measurement. The daily job touches roughly 170 measures, so
it fetches those refs rather than the repository: 171 refs of the 119th arrive in
0.9 seconds and 115 KiB, against 37 seconds and 18,046 loose ref files for every
branch. ``--filter=blob:none`` is kept because trees are all the rebuild needs --
fast-import replaces each tree wholesale -- and a blob is fetched lazily on the
rare occasion something reads one.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..gitbuild import GitRepo
from ..registry import OWNER

#: Refs per push. A single request carrying ~10,000 is rejected atomically with
#: Internal Server Error, after transferring all of them.
BATCH = 2000

#: How many times to re-push refs that did not land. Transient failures are
#: independent, so a second attempt clears nearly all of them.
ATTEMPTS = 3

#: HEAD is parked here while fetching. git refuses to fetch into the branch that
#: is checked out, and in a repository built by fast-import nothing is ever
#: checked out anyway.
PARKED_HEAD = "refs/heads/_uscongress_parked"


def repo_url(name: str, token: str = "") -> str:
    """Return the HTTPS URL of a generated repository.

    Args:
        name: Repository name without the owner.
        token: Credential to embed, for a non-interactive push.

    Returns:
        The URL.
    """
    if token:
        return f"https://x-access-token:{token}@github.com/{OWNER}/{name}.git"
    return f"https://github.com/{OWNER}/{name}.git"


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command inside a repository.

    Args:
        path: Repository directory.
        *args: Arguments after ``git``.
        check: Raise if git exits non-zero.

    Returns:
        The completed process.

    Raises:
        subprocess.CalledProcessError: If ``check`` and git failed.
    """
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def remote_refs(url: str) -> dict[str, str]:
    """Read every branch on the remote and the commit it points at.

    This is the only trustworthy account of what is published. A push that
    reported success is not evidence that it landed.

    Args:
        url: Repository URL.

    Returns:
        Branch name to commit SHA, empty if the repository does not exist.
    """
    result = subprocess.run(
        ["git", "ls-remote", "--heads", url], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return {}
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            refs[ref[len("refs/heads/") :]] = sha.strip()
    return refs


def remote_exists(url: str) -> bool:
    """Report whether the remote repository is there at all.

    Worth asking separately, because "no refs" and "no repository" look
    identical downstream: a Congress that has just convened has an empty shard,
    and so does a shard nobody has created. Pushing into the second answers
    "N refs did not land", which sends the reader looking for a transient
    failure that never happened.

    Args:
        url: Repository URL.

    Returns:
        True if the remote answers.
    """
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "-h", url],
        capture_output=True,
        text=True,
        check=False,
    )
    # 0 = refs found, 2 = reachable but empty. Anything else is unreachable.
    return result.returncode in (0, 2)


def remote_tags(url: str) -> set[str]:
    """Read every tag on the remote.

    Lets a run that holds no copy of ``us-congress-code`` still tell whether a
    release point has been built, without cloning 2.4 GB to answer it.

    Args:
        url: Repository URL.

    Returns:
        Tag names, empty if the repository does not exist.
    """
    result = subprocess.run(
        ["git", "ls-remote", "--tags", url], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return set()
    tags = set()
    for line in result.stdout.splitlines():
        _, _, ref = line.partition("\t")
        ref = ref.strip()
        if ref.startswith("refs/tags/"):
            # An annotated tag is listed twice, the second with ^{} for the
            # commit it dereferences to.
            tags.add(ref[len("refs/tags/") :].removesuffix("^{}"))
    return tags


def prepare(path: Path, url: str, branches: list[str]) -> GitRepo:
    """Make a local repository holding just the branches about to be rebuilt.

    Only branches that exist on the remote are asked for: a measure introduced
    today has no ref there yet, and naming it makes git fail the whole fetch
    rather than skip it.

    For repositories written through fast-import, which never use a working
    tree. HEAD is parked on a branch nothing fetches, because git refuses to
    fetch into the branch that is checked out and a fresh ``git init`` puts HEAD
    on ``main`` -- one of the refs that has to come down. Do not point this at
    ``us-congress-code``, which is built through its working tree.

    Args:
        path: Where to build the local repository.
        url: Remote to fetch from.
        branches: Branch names the run intends to rebuild.

    Returns:
        The prepared repository.
    """
    repo = GitRepo(path)
    repo.init()
    _git(path, "symbolic-ref", "HEAD", PARKED_HEAD)

    existing = _git(path, "remote", check=False).stdout.split()
    if "origin" not in existing:
        _git(path, "remote", "add", "origin", url)
    else:
        _git(path, "remote", "set-url", "origin", url)

    published = set(remote_refs(url))
    # `main` carries the README, the licence and GAPS.md. The rebuild never
    # writes it, but anything that later reads or amends it needs it present.
    wanted = sorted({b for b in branches if b in published} | ({"main"} & published))
    if not wanted:
        return repo

    for start in range(0, len(wanted), BATCH):
        window = wanted[start : start + BATCH]
        _git(
            path,
            "fetch",
            "--quiet",
            "--filter=blob:none",
            "--no-tags",
            "origin",
            *(f"+refs/heads/{b}:refs/heads/{b}" for b in window),
        )
    return repo


@dataclass
class PushReport:
    """What a push actually achieved, read back from the remote.

    Attributes:
        pushed: Branches whose remote SHA now matches local.
        missing: Branches that still do not match after every attempt.
        attempts: How many rounds were needed.
    """

    pushed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    attempts: int = 0

    @property
    def ok(self) -> bool:
        """Whether everything asked for landed."""
        return not self.missing


def push(
    path: Path,
    url: str,
    branches: list[str],
    batch: int = BATCH,
    attempts: int = ATTEMPTS,
) -> PushReport:
    """Push branches and verify from the remote that they landed.

    Refs are force-pushed. A rebuilt measure's commits are new objects with new
    SHAs -- a correction cannot be expressed as a fast-forward -- so refusing
    non-fast-forwards here would reject exactly the updates this exists to make.

    Args:
        path: Local repository.
        url: Remote to push to.
        branches: Branch names to publish.
        batch: Refs per request.
        attempts: How many rounds of re-pushing the difference.

    Returns:
        What landed and what did not.
    """
    report = PushReport()
    if not branches:
        return report

    local = GitRepo(path).ref_map()
    outstanding = [b for b in branches if b in local]

    for round_number in range(1, attempts + 1):
        if not outstanding:
            break
        report.attempts = round_number
        for start in range(0, len(outstanding), batch):
            window = outstanding[start : start + batch]
            # Failure here is not fatal: git reports success for refs that did
            # not land and failure for a batch in which most did, so the remote
            # is asked either way.
            _git(
                path,
                "push",
                "--quiet",
                url,
                *(f"+refs/heads/{b}:refs/heads/{b}" for b in window),
                check=False,
            )

        published = remote_refs(url)
        outstanding = [b for b in outstanding if published.get(b) != local[b]]

    landed = remote_refs(url)
    # `b in local` is not redundant. Without it a branch that exists in neither
    # place compares None to None and is reported as published -- the one answer
    # this function must never give, since its whole job is to distrust the
    # push's own account of itself.
    report.pushed = sorted(b for b in branches if b in local and landed.get(b) == local[b])
    report.missing = sorted(set(branches) - set(report.pushed))
    return report


def default_branch_is_main(name: str) -> bool:
    """Ensure a repository's default branch is ``main``.

    GitHub sets the default to the first branch pushed, which for a bills
    repository is ``hconres-1`` -- so every visitor lands on a random bill
    instead of the README.

    Args:
        name: Repository name without the owner.

    Returns:
        True if it is now ``main``, False if it could not be set.
    """
    current = subprocess.run(
        ["gh", "api", f"repos/{OWNER}/{name}", "--jq", ".default_branch"],
        capture_output=True,
        text=True,
        check=False,
    )
    if current.returncode != 0:
        return False
    if current.stdout.strip() == "main":
        return True
    patched = subprocess.run(
        ["gh", "api", "-X", "PATCH", f"repos/{OWNER}/{name}", "-f", "default_branch=main"],
        capture_output=True,
        text=True,
        check=False,
    )
    return patched.returncode == 0
